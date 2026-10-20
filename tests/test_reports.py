from auditer.models import ProfileData, ReadmeData, RepoInfo
from auditer.reports.markdown import _repo_tags, _truncate, generate_audit_report
from auditer.reports.profile import _format_event, _language_bar, generate_profile_readme
from auditer.reports.readme import (
    _build_tree,
    _detect_framework,
    _extract_key_dependencies,
    generate_repo_readme,
    scan_repo,
)


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert _truncate("hello", 5) == "hello"

    def test_long_text_truncated(self):
        result = _truncate("a" * 20, 10)
        assert result.endswith("...")
        assert len(result) == 10

    def test_very_short_max(self):
        result = _truncate("hello world", 4)
        assert result.endswith("...")
        assert len(result) == 4


class TestRepoTags:
    def test_archived_repo_gets_tag(self, sample_api_repo):
        sample_api_repo["archived"] = True
        repo = RepoInfo.from_api_response(sample_api_repo, has_readme=True)
        tags = _repo_tags(repo)
        assert "_(archived)_" in tags

    def test_fork_gets_tag(self, sample_api_repo_fork):
        repo = RepoInfo.from_api_response(sample_api_repo_fork, has_readme=True)
        tags = _repo_tags(repo)
        assert "_(fork)_" in tags

    def test_normal_repo_no_tags(self, sample_api_repo):
        repo = RepoInfo.from_api_response(sample_api_repo, has_readme=True)
        tags = _repo_tags(repo)
        assert tags == ""


class TestGenerateAuditReport:
    def test_report_has_header(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "# GitHub Audit Report" in report
        assert "testuser" in report

    def test_report_has_summary_table(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Summary" in report
        assert "Total repos" in report
        assert "| Public |" in report
        assert "| Private |" in report

    def test_report_has_public_repos(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Public Repositories" in report
        assert "my-repo" in report

    def test_report_has_private_repos(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Private Repositories" in report
        assert "secret-project" in report

    def test_report_has_missing_readme(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Repos Missing a README" in report

    def test_report_has_missing_license(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Repos Missing a License" in report

    def test_report_has_missing_description(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Repos Missing a Description" in report

    def test_report_has_stale_repos(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Stale Repos" in report

    def test_report_has_pin_recommendations(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "## Recommended Repos to Pin" in report

    def test_report_marks_forks(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "_(fork)_" in report

    def test_report_shows_missing_as_bold(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "**MISSING**" in report

    def test_report_has_health_column(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        # Health column in both private and public repo table headers
        assert "| Health |" in report
        # Verify integer scores appear in the table rows
        for repo in sample_audit_result.repos:
            assert f"| {repo.health_score()} |" in report

    def test_report_has_health_summary(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        assert "Average health" in report
        assert "Low health (<50)" in report

    def test_report_has_low_health_section(self, sample_audit_result):
        report = generate_audit_report(sample_audit_result)
        # The sample_audit_result has a stale private repo with no readme/license/desc
        # which should have a low health score
        low = sample_audit_result.low_health_repos
        if low:
            assert "## Low Health Repos (<50)" in report


class TestFormatEvent:
    def test_push_event(self):
        event = {"type": "PushEvent", "repo": {"name": "user/repo"}, "payload": {}}
        result = _format_event(event)
        assert result == "Pushed to [user/repo](https://github.com/user/repo)"

    def test_pr_opened(self):
        event = {
            "type": "PullRequestEvent",
            "repo": {"name": "user/repo"},
            "payload": {"action": "opened", "pull_request": {"number": 42}},
        }
        result = _format_event(event)
        assert "PR #42" in result

    def test_pr_closed_skipped(self):
        event = {
            "type": "PullRequestEvent",
            "repo": {"name": "user/repo"},
            "payload": {"action": "closed", "pull_request": {"number": 1}},
        }
        assert _format_event(event) is None

    def test_create_repo(self):
        event = {
            "type": "CreateEvent",
            "repo": {"name": "user/new-repo"},
            "payload": {"ref_type": "repository"},
        }
        result = _format_event(event)
        assert "Created" in result
        assert "user/new-repo" in result

    def test_create_branch_skipped(self):
        event = {
            "type": "CreateEvent",
            "repo": {"name": "user/repo"},
            "payload": {"ref_type": "branch"},
        }
        assert _format_event(event) is None

    def test_issues_opened(self):
        event = {
            "type": "IssuesEvent",
            "repo": {"name": "user/repo"},
            "payload": {"action": "opened", "issue": {"number": 7}},
        }
        result = _format_event(event)
        assert "issue #7" in result

    def test_watch_event(self):
        event = {"type": "WatchEvent", "repo": {"name": "user/repo"}, "payload": {}}
        result = _format_event(event)
        assert "Starred" in result

    def test_fork_event(self):
        event = {"type": "ForkEvent", "repo": {"name": "user/repo"}, "payload": {}}
        result = _format_event(event)
        assert "Forked" in result

    def test_unknown_event_skipped(self):
        event = {"type": "DeleteEvent", "repo": {"name": "user/repo"}, "payload": {}}
        assert _format_event(event) is None


class TestLanguageBar:
    def test_full_bar(self):
        bar = _language_bar(100.0)
        assert len(bar) == 25

    def test_half_bar(self):
        bar = _language_bar(50.0)
        assert len(bar) == 12 or len(bar) == 13  # rounding

    def test_zero_bar(self):
        bar = _language_bar(0.0)
        assert bar == ""


class TestGenerateProfileReadme:
    def test_header_with_name(self, sample_profile_data):
        report = generate_profile_readme(sample_profile_data)
        assert "# Hi, I'm Test User" in report

    def test_header_username_fallback(self, sample_profile_data):
        sample_profile_data.name = ""
        report = generate_profile_readme(sample_profile_data)
        assert "# Hi, I'm testuser" in report

    def test_about_section(self, sample_profile_data):
        report = generate_profile_readme(sample_profile_data)
        assert "## About" in report
        assert "I build things." in report
        assert "London, UK" in report
        assert "Acme Corp" in report
        assert "testuser.dev" in report
        assert "@testuser" in report

    def test_about_section_omitted(self, sample_profile_data):
        sample_profile_data.bio = ""
        sample_profile_data.location = ""
        sample_profile_data.company = ""
        sample_profile_data.blog = ""
        sample_profile_data.twitter_username = ""
        report = generate_profile_readme(sample_profile_data)
        assert "## About" not in report

    def test_stats_table(self, sample_profile_data):
        report = generate_profile_readme(sample_profile_data)
        assert "## Stats" in report
        assert "Public repos" in report
        assert "Total stars" in report
        assert "Followers" in report

    def test_top_repos_table(self, sample_profile_data):
        report = generate_profile_readme(sample_profile_data)
        assert "## Top Repositories" in report
        assert "my-repo" in report

    def test_top_repos_excludes_private(self, sample_profile_data):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        sample_profile_data.repos = [
            RepoInfo(
                name="private-repo", owner="u", url="u", private=True,
                description="", language="", stars=100, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=False, archived=False, topics=[], default_branch="main",
                size_kb=0,
            ),
        ]
        report = generate_profile_readme(sample_profile_data)
        assert "private-repo" not in report

    def test_language_breakdown(self, sample_profile_data):
        report = generate_profile_readme(sample_profile_data)
        assert "## Languages" in report
        assert "Python" in report

    def test_language_no_languages(self, sample_profile_data):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        sample_profile_data.repos = [
            RepoInfo(
                name="no-lang", owner="u", url="u", private=False,
                description="", language="", stars=0, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=False, archived=False, topics=[], default_branch="main",
                size_kb=0,
            ),
        ]
        report = generate_profile_readme(sample_profile_data)
        assert "## Languages" not in report

    def test_recent_activity(self, sample_profile_data):
        report = generate_profile_readme(sample_profile_data)
        assert "## Recent Activity" in report
        assert "Pushed to" in report
        assert "Opened PR #42" in report
        assert "Starred" in report

    def test_activity_omitted_when_empty(self, sample_profile_data):
        sample_profile_data.events = []
        report = generate_profile_readme(sample_profile_data)
        assert "## Recent Activity" not in report

    def test_footer(self, sample_profile_data):
        report = generate_profile_readme(sample_profile_data)
        assert "Generated by GitHub Auditer" in report


def _make_readme_data(**kwargs) -> ReadmeData:
    defaults = {
        "name": "my-repo",
        "owner": "testuser",
        "url": "https://github.com/testuser/my-repo",
        "description": "A test repo",
        "language": "Python",
        "topics": ["python", "cli"],
        "license": "MIT",
        "stars": 10,
        "forks": 2,
        "default_branch": "main",
        "is_fork": False,
        "tree": ["├── src/", "├── tests/", "└── pyproject.toml"],
        "package_info": {"manager": "pip", "name": "my-repo", "scripts": {}},
        "has_tests": True,
        "has_ci": True,
        "has_contributing": False,
        "has_changelog": False,
        "entry_points": ["cli.py"],
        "detected_languages": [
            {"language": "Python", "count": 10, "percentage": 100.0},
        ],
        "install_command": "pip install .",
        "test_command": "pytest",
    }
    defaults.update(kwargs)
    return ReadmeData(**defaults)


class TestGenerateRepoReadme:
    def test_title_and_description(self):
        data = _make_readme_data()
        report = generate_repo_readme(data)
        assert "# my-repo" in report
        assert "A test repo" in report

    def test_description_from_package_info(self):
        data = _make_readme_data(
            description="",
            package_info={"manager": "pip", "description": "From pyproject", "scripts": {}},
        )
        report = generate_repo_readme(data)
        assert "From pyproject" in report

    def test_topics_section(self):
        data = _make_readme_data(topics=["python", "cli", "audit"])
        report = generate_repo_readme(data)
        assert "`python`" in report
        assert "`cli`" in report
        assert "`audit`" in report

    def test_topics_omitted_when_empty(self):
        data = _make_readme_data(topics=[])
        report = generate_repo_readme(data)
        # No topic badges line (backtick-wrapped words separated by spaces)
        lines = report.split("\n")
        topic_lines = [l for l in lines if l.startswith("`") and "` `" in l]
        assert len(topic_lines) == 0

    def test_installation_section(self):
        data = _make_readme_data(install_command="pip install .")
        report = generate_repo_readme(data)
        assert "## Installation" in report
        assert "pip install ." in report

    def test_installation_omitted(self):
        data = _make_readme_data(install_command="")
        report = generate_repo_readme(data)
        assert "## Installation" not in report

    def test_usage_section_with_entry_points(self):
        data = _make_readme_data(entry_points=["cli.py", "bin/"])
        report = generate_repo_readme(data)
        assert "## Usage" in report
        assert "`cli.py`" in report
        assert "`bin/`" in report

    def test_usage_section_with_scripts(self):
        data = _make_readme_data(
            entry_points=[],
            package_info={"manager": "npm", "scripts": {"build": "tsc", "dev": "nodemon"}},
        )
        report = generate_repo_readme(data)
        assert "## Usage" in report
        assert "`build`" in report
        assert "`tsc`" in report

    def test_usage_omitted(self):
        data = _make_readme_data(entry_points=[], package_info={})
        report = generate_repo_readme(data)
        assert "## Usage" not in report

    def test_project_structure(self):
        data = _make_readme_data(tree=["├── src/", "├── tests/", "└── pyproject.toml"])
        report = generate_repo_readme(data)
        assert "## Project Structure" in report
        assert "├── src/" in report
        assert "├── tests/" in report
        assert "└── pyproject.toml" in report

    def test_testing_section(self):
        data = _make_readme_data(test_command="pytest")
        report = generate_repo_readme(data)
        assert "## Testing" in report
        assert "pytest" in report

    def test_testing_omitted(self):
        data = _make_readme_data(test_command="")
        report = generate_repo_readme(data)
        assert "## Testing" not in report

    def test_license_section(self):
        data = _make_readme_data(license="MIT")
        report = generate_repo_readme(data)
        assert "## License" in report
        assert "MIT" in report

    def test_license_omitted(self):
        data = _make_readme_data(license="")
        report = generate_repo_readme(data)
        assert "## License" not in report

    def test_footer(self):
        data = _make_readme_data()
        report = generate_repo_readme(data)
        assert "Generated by GitHub Auditer" in report

    def test_stats_line(self):
        data = _make_readme_data(stars=42, forks=7, language="Python")
        report = generate_repo_readme(data)
        assert "Stars: 42" in report
        assert "Forks: 7" in report
        assert "Python" in report

    def test_stats_omitted_when_zero(self):
        data = _make_readme_data(stars=0, forks=0, language="")
        report = generate_repo_readme(data)
        assert "Stars:" not in report
        assert "Forks:" not in report

    def test_framework_section(self):
        data = _make_readme_data(
            package_info={"manager": "composer", "scripts": {}, "framework": "Symfony"},
        )
        report = generate_repo_readme(data)
        assert "**Framework:** Symfony" in report

    def test_dependencies_section(self):
        data = _make_readme_data(
            package_info={
                "manager": "npm",
                "scripts": {},
                "key_dependencies": ["express", "lodash", "axios"],
            },
        )
        report = generate_repo_readme(data)
        assert "## Dependencies" in report
        assert "`express`" in report
        assert "`lodash`" in report
        assert "`axios`" in report

    def test_languages_section(self):
        data = _make_readme_data(
            detected_languages=[
                {"language": "Python", "count": 15, "percentage": 75.0},
                {"language": "JavaScript", "count": 5, "percentage": 25.0},
            ],
        )
        report = generate_repo_readme(data)
        assert "## Languages" in report
        assert "| Python | 15 | 75.0% |" in report
        assert "| JavaScript | 5 | 25.0% |" in report

    def test_nested_scripts_handled(self):
        """Dict and list script values don't crash or render raw."""
        data = _make_readme_data(
            entry_points=[],
            package_info={
                "manager": "composer",
                "scripts": {
                    "test": "phpunit",
                    "cache:clear": {"symfony-cmd": ["cache:clear"]},
                    "post-install": ["@auto-scripts"],
                },
            },
        )
        report = generate_repo_readme(data)
        # String script renders normally
        assert "`test`: `phpunit`" in report
        # Dict value does NOT render as raw Python repr
        assert "{'symfony-cmd'" not in report
        assert "symfony-cmd" not in report
        # List value renders first item
        assert "`post-install`: `@auto-scripts`" in report


class TestScanRepo:
    def test_detects_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "my-app", "version": "1.0.0", "description": "Test", '
            '"scripts": {"test": "jest", "build": "tsc"}, '
            '"dependencies": {"express": "^4.0"}, "devDependencies": {"jest": "^29"}}'
        )
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "npm"
        assert result["package_info"]["name"] == "my-app"
        assert result["install_command"] == "npm install"
        assert result["test_command"] == "npm test"

    def test_detects_composer_json(self, tmp_path):
        (tmp_path / "composer.json").write_text(
            '{"name": "vendor/pkg", "description": "A PHP package", '
            '"type": "library", "scripts": {"test": "phpunit"}}'
        )
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "composer"
        assert result["install_command"] == "composer install"
        assert result["test_command"] == "composer test"

    def test_detects_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "my-tool"\ndescription = "A Python tool"\n'
            'dependencies = ["click"]\n\n'
            '[project.optional-dependencies]\ndev = ["pytest"]\n'
        )
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "pip"
        assert result["package_info"]["name"] == "my-tool"
        assert result["install_command"] == 'pip install -e ".[dev]"'
        assert result["test_command"] == "pytest"

    def test_detects_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "my-crate"\ndescription = "A Rust crate"\n'
        )
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "cargo"
        assert result["install_command"] == "cargo build"
        assert result["test_command"] == "cargo test"

    def test_detects_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text(
            "module github.com/user/myapp\n\ngo 1.21\n"
        )
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "go"
        assert result["install_command"] == "go build ./..."
        assert result["test_command"] == "go test ./..."

    def test_detects_test_dir(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_a(): pass\n")
        result = scan_repo(tmp_path)
        assert result["has_tests"] is True

    def test_no_tests_detected(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')\n")
        result = scan_repo(tmp_path)
        assert result["has_tests"] is False

    def test_detects_ci(self, tmp_path):
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: CI\n")
        result = scan_repo(tmp_path)
        assert result["has_ci"] is True

    def test_no_ci_detected(self, tmp_path):
        (tmp_path / "src").mkdir()
        result = scan_repo(tmp_path)
        assert result["has_ci"] is False

    def test_detects_entry_points(self, tmp_path):
        (tmp_path / "index.js").write_text("module.exports = {}\n")
        (tmp_path / "bin").mkdir()
        result = scan_repo(tmp_path)
        assert "index.js" in result["entry_points"]
        assert "bin/" in result["entry_points"]

    def test_language_detection(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hi')\n")
        (tmp_path / "utils.py").write_text("x = 1\n")
        (tmp_path / "app.js").write_text("console.log('hi')\n")
        result = scan_repo(tmp_path)
        langs = result["detected_languages"]
        lang_names = [l["language"] for l in langs]
        assert "Python" in lang_names
        assert "JavaScript" in lang_names
        # Python should be first (more files)
        assert langs[0]["language"] == "Python"
        assert langs[0]["count"] == 2
        assert langs[1]["language"] == "JavaScript"
        assert langs[1]["count"] == 1

    def test_tree_excludes_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "README.md").write_text("# Hi\n")
        result = scan_repo(tmp_path)
        tree_text = "\n".join(result["tree"])
        assert ".git" not in tree_text
        assert "src/" in tree_text
        assert "README.md" in tree_text

    def test_tree_excludes_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "index.js").write_text("x = 1\n")
        result = scan_repo(tmp_path)
        tree_text = "\n".join(result["tree"])
        assert "node_modules" not in tree_text
        assert "index.js" in tree_text

    def test_detects_contributing(self, tmp_path):
        (tmp_path / "CONTRIBUTING.md").write_text("# Contributing\n")
        result = scan_repo(tmp_path)
        assert result["has_contributing"] is True

    def test_detects_changelog(self, tmp_path):
        (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
        result = scan_repo(tmp_path)
        assert result["has_changelog"] is True

    def test_package_json_priority_over_pyproject(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "app", "scripts": {}}')
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\n')
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "npm"

    def test_empty_repo(self, tmp_path):
        result = scan_repo(tmp_path)
        assert result["tree"] == []
        assert result["package_info"] == {}
        assert result["has_tests"] is False
        assert result["has_ci"] is False
        assert result["install_command"] == ""
        assert result["test_command"] == ""

    def test_pyproject_without_dev_deps(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "simple"\ndescription = "Simple"\n'
            'dependencies = ["requests"]\n'
        )
        result = scan_repo(tmp_path)
        assert result["install_command"] == "pip install ."
        assert result["test_command"] == ""

    def test_detects_phpunit(self, tmp_path):
        (tmp_path / "phpunit.xml").write_text("<phpunit/>\n")
        result = scan_repo(tmp_path)
        assert result["has_tests"] is True

    def test_detects_test_files(self, tmp_path):
        (tmp_path / "main_test.go").write_text("package main\n")
        result = scan_repo(tmp_path)
        assert result["has_tests"] is True

    def test_tree_is_hierarchical(self, tmp_path):
        """Tree uses box-drawing chars and shows nested directories."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "controllers").mkdir()
        (tmp_path / "src" / "models").mkdir()
        (tmp_path / "src" / "controllers" / "main.py").write_text("")
        (tmp_path / "src" / "models" / "user.py").write_text("")
        (tmp_path / "README.md").write_text("# Hi\n")
        result = scan_repo(tmp_path)
        tree_text = "\n".join(result["tree"])
        # Must contain box-drawing characters
        assert "├──" in tree_text or "└──" in tree_text
        # Must show nested structure
        assert "controllers/" in tree_text
        assert "models/" in tree_text
        # Nested items should have indent prefix
        assert "│   " in tree_text or "    " in tree_text

    def test_framework_detection_symfony(self, tmp_path):
        """Detects Symfony via composer.json require."""
        (tmp_path / "composer.json").write_text(
            '{"name": "vendor/app", "require": {"symfony/framework-bundle": "^6.0"}}'
        )
        result = scan_repo(tmp_path)
        assert result["framework"] == "Symfony"

    def test_framework_detection_react(self, tmp_path):
        """Detects React via package.json dependencies."""
        (tmp_path / "package.json").write_text(
            '{"name": "my-app", "dependencies": {"react": "^18.0", "react-dom": "^18.0"}}'
        )
        result = scan_repo(tmp_path)
        assert result["framework"] == "React"

    def test_framework_detection_django(self, tmp_path):
        """Detects Django via pyproject.toml dependencies."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mysite"\ndescription = "A Django site"\n'
            'dependencies = ["django>=4.2"]\n'
        )
        result = scan_repo(tmp_path)
        assert result["framework"] == "Django"

    def test_key_dependencies_extracted(self, tmp_path):
        """Production deps included, build tools filtered."""
        (tmp_path / "package.json").write_text(
            '{"name": "app", "dependencies": {"express": "^4.0", "lodash": "^4.0"}, '
            '"devDependencies": {"jest": "^29", "eslint": "^8"}}'
        )
        result = scan_repo(tmp_path)
        key_deps = result["key_dependencies"]
        assert "express" in key_deps
        assert "lodash" in key_deps
        # Build tools should be filtered
        assert "jest" not in key_deps
        assert "eslint" not in key_deps

    def test_no_framework_when_absent(self, tmp_path):
        """No framework detected for a simple Python package."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "simple"\ndescription = "Simple"\n'
            'dependencies = ["click", "httpx"]\n'
        )
        result = scan_repo(tmp_path)
        assert result["framework"] is None


class TestBuildTree:
    def test_simple_flat(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        lines = _build_tree(tmp_path)
        assert lines == ["├── a.txt", "└── b.txt"]

    def test_dirs_sorted_first(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "README.md").write_text("")
        lines = _build_tree(tmp_path)
        assert "src/" in lines[0]
        assert "README.md" in lines[1]

    def test_nested_structure(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")
        lines = _build_tree(tmp_path)
        assert len(lines) == 2
        assert "└── src/" in lines[0]
        assert "    └── main.py" in lines[1]

    def test_max_depth_respected(self, tmp_path):
        d = tmp_path
        for name in ("a", "b", "c", "d"):
            d = d / name
            d.mkdir()
        (d / "deep.txt").write_text("")
        lines = _build_tree(tmp_path, max_depth=2)
        tree_text = "\n".join(lines)
        assert "a/" in tree_text
        assert "b/" in tree_text
        assert "c/" not in tree_text

    def test_excludes_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        lines = _build_tree(tmp_path)
        tree_text = "\n".join(lines)
        assert ".git" not in tree_text
        assert "src/" in tree_text


class TestDetectFramework:
    def test_symfony(self):
        pkg = {"require": ["symfony/framework-bundle"]}
        assert _detect_framework(pkg) == "Symfony"

    def test_laravel(self):
        pkg = {"require": ["laravel/framework"]}
        assert _detect_framework(pkg) == "Laravel"

    def test_react(self):
        pkg = {"dependencies": ["react", "react-dom"]}
        assert _detect_framework(pkg) == "React"

    def test_django_with_version(self):
        pkg = {"dependencies": ["django>=4.2"]}
        assert _detect_framework(pkg) == "Django"

    def test_no_framework(self):
        pkg = {"dependencies": ["click", "httpx"]}
        assert _detect_framework(pkg) is None

    def test_empty_package_info(self):
        assert _detect_framework({}) is None
        assert _detect_framework(None) is None

    def test_drupal(self):
        pkg = {"require": ["drupal/core-recommended"]}
        assert _detect_framework(pkg) == "Drupal"


class TestExtractKeyDependencies:
    def test_basic_extraction(self):
        pkg = {"dependencies": ["express", "lodash", "axios"]}
        deps = _extract_key_dependencies(pkg)
        assert deps == ["express", "lodash", "axios"]

    def test_filters_build_tools(self):
        pkg = {"dependencies": ["express", "webpack", "eslint"]}
        deps = _extract_key_dependencies(pkg)
        assert "express" in deps
        assert "webpack" not in deps
        assert "eslint" not in deps

    def test_filters_php_platform(self):
        pkg = {"require": ["php", "ext-json", "symfony/http-kernel"]}
        deps = _extract_key_dependencies(pkg)
        assert "symfony/http-kernel" in deps
        assert "php" not in deps
        assert "ext-json" not in deps

    def test_top_n_limit(self):
        pkg = {"dependencies": [f"dep-{i}" for i in range(20)]}
        deps = _extract_key_dependencies(pkg, top_n=5)
        assert len(deps) == 5

    def test_empty_package_info(self):
        assert _extract_key_dependencies({}) == []
        assert _extract_key_dependencies(None) == []

    def test_python_version_specifiers(self):
        pkg = {"dependencies": ["django>=4.2", "celery[redis]>=5.0"]}
        deps = _extract_key_dependencies(pkg)
        assert "django" in deps
        assert "celery" in deps


class TestLanguageBarBounds:
    def test_negative_percentage_clamped(self):
        bar = _language_bar(-10.0)
        assert bar == ""
        assert len(bar) == 0

    def test_over_100_percentage_clamped(self):
        bar = _language_bar(200.0)
        assert len(bar) == 25  # Clamped to max_width

    def test_custom_max_width(self):
        bar = _language_bar(50.0, max_width=10)
        assert len(bar) == 5


class TestBuildTreeMaxLines:
    def test_tree_respects_max_lines(self, tmp_path):
        # Create 20 files — tree should be capped at 5 lines
        for i in range(20):
            (tmp_path / f"file_{i:02d}.txt").write_text("")
        lines = _build_tree(tmp_path, max_lines=5)
        assert len(lines) <= 5

    def test_tree_respects_max_lines_with_subdirs(self, tmp_path):
        # Create nested structure that would produce many lines
        for i in range(10):
            d = tmp_path / f"dir_{i:02d}"
            d.mkdir()
            for j in range(5):
                (d / f"file_{j}.txt").write_text("")
        lines = _build_tree(tmp_path, max_lines=15)
        assert len(lines) <= 15

    def test_tree_default_max_lines_is_generous(self, tmp_path):
        # Small tree should not be affected by default cap
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")
        lines = _build_tree(tmp_path)
        assert len(lines) == 2


class TestMalformedPyprojectToml:
    def test_malformed_toml_falls_back_to_regex(self, tmp_path):
        # Invalid TOML syntax: missing closing bracket
        (tmp_path / "pyproject.toml").write_text(
            '[project\nname = "broken-app"\ndescription = "A broken config"\n'
        )
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "pip"
        # Regex fallback should still extract name
        assert result["package_info"]["name"] == "broken-app"

    def test_empty_toml_falls_back_to_regex(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        result = scan_repo(tmp_path)
        assert result["package_info"]["manager"] == "pip"
        assert result["package_info"]["name"] == ""
