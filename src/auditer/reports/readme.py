from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from auditer.models import ReadmeData

# Directories to exclude from tree and language scanning
_EXCLUDED_DIRS = {
    ".git", "node_modules", "vendor", "__pycache__", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".tox", ".eggs", "dist", "build",
    ".next", ".nuxt", "venv", ".venv", "env",
}

# File extension to language name mapping
_EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".lua": "Lua",
    ".r": "R",
    ".R": "R",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".pl": "Perl",
    ".pm": "Perl",
}

# Max depth for recursive language detection
_MAX_SCAN_DEPTH = 3

# Max lines for tree output in generated README
_MAX_TREE_LINES = 50

# Framework detection mapping: dependency name -> framework name
_FRAMEWORK_MAP: dict[str, str] = {
    # PHP
    "symfony/framework-bundle": "Symfony",
    "laravel/framework": "Laravel",
    "drupal/core": "Drupal",
    "drupal/core-recommended": "Drupal",
    # JavaScript/TypeScript
    "react": "React",
    "react-dom": "React",
    "vue": "Vue.js",
    "next": "Next.js",
    "express": "Express.js",
    "@angular/core": "Angular",
    "@nestjs/core": "NestJS",
    "nuxt": "Nuxt.js",
    "svelte": "Svelte",
    "gatsby": "Gatsby",
    # Python
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "tornado": "Tornado",
    "starlette": "Starlette",
}

# Dependencies to filter out when extracting key deps
_BUILD_TOOL_PATTERNS = {
    "webpack", "eslint", "prettier", "babel", "jest", "mocha", "chai",
    "phpunit", "pytest", "tox", "mypy", "flake8", "black", "isort",
    "ruff", "pylint", "phpstan", "phpcs", "php-cs-fixer",
    "typescript", "ts-node", "nodemon", "concurrently",
    "@types/", "rollup", "vite", "esbuild", "parcel",
    "sass", "less", "postcss", "autoprefixer", "tailwindcss",
    "husky", "lint-staged", "commitlint",
}


def _count_languages(repo_path: Path, depth: int = 0) -> Counter:
    """Count file extensions recursively up to _MAX_SCAN_DEPTH."""
    counts: Counter = Counter()
    if depth > _MAX_SCAN_DEPTH:
        return counts
    try:
        entries = list(repo_path.iterdir())
    except PermissionError:
        return counts
    for entry in entries:
        if entry.name.startswith(".") and entry.is_dir():
            continue
        if entry.is_dir():
            if entry.name in _EXCLUDED_DIRS:
                continue
            counts += _count_languages(entry, depth + 1)
        elif entry.is_file():
            ext = entry.suffix.lower()
            lang = _EXT_TO_LANG.get(ext)
            if lang:
                counts[lang] += 1
    return counts


_MAX_BUILD_TREE_LINES = 500  # Safety cap on tree output during scanning


def _build_tree(
    repo_path: Path,
    max_depth: int = 3,
    max_lines: int = _MAX_BUILD_TREE_LINES,
    _prefix: str = "",
    _depth: int = 0,
) -> list[str]:
    """Build a box-drawing tree of the directory structure.

    Returns lines like:
        ├── config/
        │   ├── packages/
        │   └── routes/
        ├── src/
        └── templates/
    """
    if _depth >= max_depth:
        return []
    try:
        entries = sorted(repo_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return []

    # Filter out excluded and hidden dirs/files at top level
    visible = [
        e for e in entries
        if not (e.name.startswith(".") and e.is_dir())
        and e.name not in _EXCLUDED_DIRS
    ]

    lines: list[str] = []
    for i, entry in enumerate(visible):
        if len(lines) >= max_lines:
            break
        is_last = i == len(visible) - 1
        connector = "└── " if is_last else "├── "
        name = entry.name + ("/" if entry.is_dir() else "")
        lines.append(f"{_prefix}{connector}{name}")

        if entry.is_dir() and entry.name not in _EXCLUDED_DIRS:
            remaining = max_lines - len(lines)
            if remaining > 0:
                extension = "    " if is_last else "│   "
                sub_lines = _build_tree(entry, max_depth, remaining, _prefix + extension, _depth + 1)
                lines.extend(sub_lines)

    return lines


def _detect_framework(package_info: dict) -> str | None:
    """Detect framework from package dependencies."""
    if not package_info:
        return None

    # Collect all dependency names from all possible keys
    all_deps: set[str] = set()
    for key in ("dependencies", "dev_dependencies", "require", "require_dev"):
        deps = package_info.get(key, [])
        if isinstance(deps, list):
            for dep in deps:
                # Strip version specifiers for Python deps (e.g. "django>=4.0" -> "django")
                name = dep.split(">=")[0].split("<=")[0].split("==")[0].split(">")[0].split("<")[0].split("[")[0].strip()
                all_deps.add(name.lower())

    for dep_name, framework in _FRAMEWORK_MAP.items():
        if dep_name.lower() in all_deps:
            return framework
    return None


def _extract_key_dependencies(package_info: dict, top_n: int = 10) -> list[str]:
    """Extract key production dependencies, filtering out build/test tools."""
    if not package_info:
        return []

    # Get production deps (prefer 'dependencies' or 'require')
    deps: list[str] = []
    for key in ("dependencies", "require"):
        raw = package_info.get(key, [])
        if isinstance(raw, list):
            deps.extend(raw)

    # Filter out build tools and PHP platform requirements
    filtered = []
    for dep in deps:
        name = dep.split(">=")[0].split("<=")[0].split("==")[0].split(">")[0].split("<")[0].split("[")[0].strip()
        name_lower = name.lower()
        if name_lower.startswith("php") or name_lower.startswith("ext-"):
            continue
        if any(tool in name_lower for tool in _BUILD_TOOL_PATTERNS):
            continue
        filtered.append(name)

    return filtered[:top_n]


def _parse_package_json(path: Path) -> dict:
    """Parse package.json and extract relevant fields."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        "manager": "npm",
        "name": data.get("name", ""),
        "version": data.get("version", ""),
        "description": data.get("description", ""),
        "scripts": data.get("scripts", {}),
        "dependencies": list(data.get("dependencies", {}).keys()),
        "dev_dependencies": list(data.get("devDependencies", {}).keys()),
    }


def _parse_composer_json(path: Path) -> dict:
    """Parse composer.json and extract relevant fields."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        "manager": "composer",
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "type": data.get("type", ""),
        "keywords": data.get("keywords", []),
        "require": list(data.get("require", {}).keys()),
        "require_dev": list(data.get("require-dev", {}).keys()),
        "scripts": data.get("scripts", {}),
    }


def _parse_pyproject_toml(path: Path) -> dict:
    """Parse pyproject.toml using tomllib (3.11+) / tomli (3.10) or fallback regex."""
    content = path.read_text()
    data = None
    try:
        import tomllib
        data = tomllib.loads(content)
    except ModuleNotFoundError:
        try:
            import tomli
            data = tomli.loads(content)
        except (ModuleNotFoundError, Exception):
            pass
    except Exception:
        pass

    if data is not None:
        project = data.get("project", {})
        deps = project.get("dependencies", [])
        optional_deps = project.get("optional-dependencies", {})
        all_dev_deps = []
        for group_deps in optional_deps.values():
            all_dev_deps.extend(group_deps)
        return {
            "manager": "pip",
            "name": project.get("name", ""),
            "description": project.get("description", ""),
            "dependencies": deps,
            "dev_dependencies": all_dev_deps,
            "scripts": project.get("scripts", {}),
        }

    # Fallback: basic regex for name/description
    import re
    name_match = re.search(r'^name\s*=\s*"([^"]*)"', content, re.MULTILINE)
    desc_match = re.search(r'^description\s*=\s*"([^"]*)"', content, re.MULTILINE)
    return {
        "manager": "pip",
        "name": name_match.group(1) if name_match else "",
        "description": desc_match.group(1) if desc_match else "",
        "dependencies": [],
        "dev_dependencies": [],
        "scripts": {},
    }


def _parse_cargo_toml(path: Path) -> dict:
    """Basic regex parse of Cargo.toml."""
    import re
    content = path.read_text()
    name_match = re.search(r'^name\s*=\s*"([^"]*)"', content, re.MULTILINE)
    desc_match = re.search(r'^description\s*=\s*"([^"]*)"', content, re.MULTILINE)
    return {
        "manager": "cargo",
        "name": name_match.group(1) if name_match else "",
        "description": desc_match.group(1) if desc_match else "",
    }


def _parse_go_mod(path: Path) -> dict:
    """Basic regex parse of go.mod."""
    import re
    content = path.read_text()
    module_match = re.search(r'^module\s+(\S+)', content, re.MULTILINE)
    return {
        "manager": "go",
        "name": module_match.group(1) if module_match else "",
        "description": "",
    }


def scan_repo(repo_path: Path) -> dict:
    """Scan a local checkout and extract project structure information.

    Returns a dict with keys matching ReadmeData local scan fields.
    """
    # Hierarchical tree with box-drawing characters
    tree = _build_tree(repo_path, max_depth=3)

    # Package manager detection (first match wins)
    package_info: dict = {}
    install_command = ""
    test_command = ""

    if (repo_path / "package.json").is_file():
        package_info = _parse_package_json(repo_path / "package.json")
        install_command = "npm install"
        scripts = package_info.get("scripts", {})
        if "test" in scripts:
            test_command = "npm test"
    elif (repo_path / "composer.json").is_file():
        package_info = _parse_composer_json(repo_path / "composer.json")
        install_command = "composer install"
        scripts = package_info.get("scripts", {})
        if "test" in scripts:
            test_command = "composer test"
    elif (repo_path / "pyproject.toml").is_file():
        package_info = _parse_pyproject_toml(repo_path / "pyproject.toml")
        all_deps = " ".join(
            package_info.get("dependencies", [])
            + package_info.get("dev_dependencies", [])
        ).lower()
        if "pytest" in all_deps:
            test_command = "pytest"
        if package_info.get("dev_dependencies"):
            install_command = 'pip install -e ".[dev]"'
        else:
            install_command = "pip install ."
    elif (repo_path / "Cargo.toml").is_file():
        package_info = _parse_cargo_toml(repo_path / "Cargo.toml")
        install_command = "cargo build"
        test_command = "cargo test"
    elif (repo_path / "go.mod").is_file():
        package_info = _parse_go_mod(repo_path / "go.mod")
        install_command = "go build ./..."
        test_command = "go test ./..."

    # Framework and key dependencies detection
    framework = _detect_framework(package_info)
    key_dependencies = _extract_key_dependencies(package_info)

    # Test detection
    test_dirs = {"tests", "test", "spec", "__tests__"}
    has_tests = any((repo_path / d).is_dir() for d in test_dirs)
    if not has_tests:
        # Check for test files in root
        try:
            root_files = [f.name for f in repo_path.iterdir() if f.is_file()]
        except PermissionError:
            root_files = []
        test_patterns = ("_test.go", "_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts")
        has_tests = any(
            f.endswith(p) for f in root_files for p in test_patterns
        )
        if not has_tests:
            has_tests = (repo_path / "phpunit.xml").is_file() or (repo_path / "pytest.ini").is_file()

    # CI detection
    has_ci = (
        (repo_path / ".github" / "workflows").is_dir()
        or (repo_path / ".gitlab-ci.yml").is_file()
        or (repo_path / ".travis.yml").is_file()
        or (repo_path / "Jenkinsfile").is_file()
        or (repo_path / ".circleci").is_dir()
    )

    # Entry points
    entry_points: list[str] = []
    if (repo_path / "bin").is_dir():
        entry_points.append("bin/")
    for name in ("index.js", "index.ts", "main.py", "app.py", "cli.py"):
        if (repo_path / name).is_file():
            entry_points.append(name)
    for name in ("src/main.py", "src/main.rs", "src/main.go", "src/main.js", "src/main.ts"):
        if (repo_path / name).is_file():
            entry_points.append(name)

    # Contributing / Changelog detection
    lower_files = {f.name.lower() for f in repo_path.iterdir() if f.is_file()}
    has_contributing = "contributing.md" in lower_files
    has_changelog = bool(lower_files & {"changelog.md", "changes.md", "history.md"})

    # Language detection (dict format with counts)
    lang_counts = _count_languages(repo_path)
    total_files = sum(lang_counts.values())
    detected_languages = [
        {
            "language": lang,
            "count": count,
            "percentage": round(count / total_files * 100, 1) if total_files else 0,
        }
        for lang, count in lang_counts.most_common()
    ]

    return {
        "tree": tree,
        "package_info": package_info,
        "has_tests": has_tests,
        "has_ci": has_ci,
        "has_contributing": has_contributing,
        "has_changelog": has_changelog,
        "entry_points": entry_points,
        "detected_languages": detected_languages,
        "install_command": install_command,
        "test_command": test_command,
        "framework": framework,
        "key_dependencies": key_dependencies,
    }


def generate_repo_readme(data: ReadmeData) -> str:
    """Generate a Markdown README from ReadmeData."""
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Title and description
    lines.append(f"# {data.name}")
    lines.append("")
    desc = data.description or data.package_info.get("description", "")
    if desc:
        lines.append(desc)
        lines.append("")

    # 2. Stats line (stars/forks/language)
    stats_parts: list[str] = []
    if data.stars > 0:
        stats_parts.append(f"Stars: {data.stars}")
    if data.forks > 0:
        stats_parts.append(f"Forks: {data.forks}")
    if data.language:
        stats_parts.append(data.language)
    if stats_parts:
        lines.append(" | ".join(stats_parts))
        lines.append("")

    # 3. Topics
    if data.topics:
        lines.append(" ".join(f"`{t}`" for t in data.topics))
        lines.append("")

    # 4. Framework badge
    framework = data.package_info.get("framework")
    if framework:
        lines.append(f"**Framework:** {framework}")
        lines.append("")

    # 5. Installation
    if data.install_command:
        lines.append("## Installation")
        lines.append("")
        lines.append("```bash")
        lines.append(data.install_command)
        lines.append("```")
        lines.append("")

    # 6. Dependencies
    key_deps = data.package_info.get("key_dependencies", [])
    if key_deps:
        lines.append("## Dependencies")
        lines.append("")
        for dep in key_deps:
            lines.append(f"- `{dep}`")
        lines.append("")

    # 7. Usage
    usage_lines: list[str] = []
    if data.entry_points:
        usage_lines.append("**Entry points:**")
        for ep in data.entry_points:
            usage_lines.append(f"- `{ep}`")
    scripts = data.package_info.get("scripts", {})
    if scripts:
        if usage_lines:
            usage_lines.append("")
        usage_lines.append("**Available scripts:**")
        for name, cmd in scripts.items():
            if isinstance(cmd, str):
                usage_lines.append(f"- `{name}`: `{cmd}`")
            elif isinstance(cmd, list) and cmd and isinstance(cmd[0], str):
                usage_lines.append(f"- `{name}`: `{cmd[0]}`")
            # Skip dict values entirely
    if usage_lines:
        lines.append("## Usage")
        lines.append("")
        lines.extend(usage_lines)
        lines.append("")

    # 8. Project Structure (box-drawing tree, capped at _MAX_TREE_LINES)
    if data.tree:
        lines.append("## Project Structure")
        lines.append("")
        lines.append("```")
        tree_lines = data.tree[:_MAX_TREE_LINES]
        for item in tree_lines:
            lines.append(item)
        if len(data.tree) > _MAX_TREE_LINES:
            lines.append(f"... ({len(data.tree) - _MAX_TREE_LINES} more entries)")
        lines.append("```")
        lines.append("")

    # 9. Testing
    if data.test_command:
        lines.append("## Testing")
        lines.append("")
        lines.append("```bash")
        lines.append(data.test_command)
        lines.append("```")
        lines.append("")

    # 10. Languages
    if data.detected_languages:
        # Support both dict format (new) and string format (legacy)
        if isinstance(data.detected_languages[0], dict):
            lines.append("## Languages")
            lines.append("")
            lines.append("| Language | Files | % |")
            lines.append("|---|---|---|")
            for lang_info in data.detected_languages:
                lines.append(
                    f"| {lang_info['language']} | {lang_info['count']} | {lang_info['percentage']}% |"
                )
            lines.append("")

    # 11. License
    if data.license:
        lines.append("## License")
        lines.append("")
        lines.append(data.license)
        lines.append("")

    # 12. Footer
    lines.append(f"_Generated by GitHub Auditer on {now}_")
    lines.append("")

    return "\n".join(lines)
