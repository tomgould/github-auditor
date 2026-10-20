from __future__ import annotations

from datetime import datetime, timezone

from auditer.models import AccessResult

# Permission levels in descending order of privilege
PERMISSION_LEVELS = ["admin", "maintain", "push", "triage", "pull"]


def _highest_permission(permissions: dict) -> str:
    """Return the highest permission level from a collaborator's permissions dict."""
    for level in PERMISSION_LEVELS:
        if permissions.get(level):
            return level
    return "pull"


def _build_user_id_map(result: AccessResult) -> dict[str, int]:
    """Build a login -> lowest seen id mapping across all repos."""
    id_map: dict[str, int] = {}
    for repo in result.repos:
        for c in repo.collaborators:
            login = c["login"]
            uid = c.get("id", 0)
            if login not in id_map or uid < id_map[login]:
                id_map[login] = uid
    return id_map


def _sort_users(users: list[str], sort_by: str, id_map: dict[str, int]) -> list[str]:
    """Sort user list by name (alphabetical) or id (ascending)."""
    if sort_by == "id":
        return sorted(users, key=lambda u: id_map.get(u, 0))
    return sorted(users, key=lambda u: u.lower())


def generate_access_report(result: AccessResult, sort_by: str = "name") -> str:
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Header
    lines.append(f"# Access Report \u2014 `{result.account_username}`")
    lines.append(f"_Generated on {now}_")
    lines.append("")

    if not result.repos:
        lines.append("No repositories found.")
        lines.append("")
        return "\n".join(lines)

    # Build user ID map for id-based sorting
    user_id_map = _build_user_id_map(result)

    # --- View 1: Summary Matrix ---
    lines.append("## Access Matrix")
    if sort_by == "id":
        lines.append("_Sorted by GitHub user ID (ascending — lower ID = older account)_")
    lines.append("")

    users = result.unique_users
    repos_sorted = sorted(result.repos, key=lambda r: r.repo_name.lower())

    if not users:
        lines.append("No collaborators found.")
        lines.append("")
    else:
        sorted_users = _sort_users(users, sort_by, user_id_map)

        # Header row
        repo_names = [r.repo_name for r in repos_sorted]
        lines.append("| User | ID | " + " | ".join(repo_names) + " |")
        lines.append("|---|---" + "|---" * len(repo_names) + "|")

        # Build lookup: repo_name -> {login: highest_permission}
        repo_perms: dict[str, dict[str, str]] = {}
        for repo in repos_sorted:
            perms: dict[str, str] = {}
            for c in repo.collaborators:
                perms[c["login"]] = _highest_permission(c.get("permissions", {}))
            repo_perms[repo.repo_name] = perms

        # Data rows
        for user in sorted_users:
            uid = str(user_id_map.get(user, ""))
            cells = []
            for repo in repos_sorted:
                perm = repo_perms[repo.repo_name].get(user)
                cells.append(perm if perm else "\u2014")
            lines.append(f"| {user} | {uid} | " + " | ".join(cells) + " |")
        lines.append("")

    # --- View 2: Per-Repo Detail Tables ---
    lines.append("## Per-Repo Details")
    lines.append("")

    for repo in repos_sorted:
        visibility = "_(private)_" if repo.private else "_(public)_"
        lines.append(f"### [{repo.repo_name}]({repo.repo_url}) {visibility}")
        lines.append("")

        if not repo.collaborators:
            lines.append("No collaborators.")
            lines.append("")
            continue

        lines.append("| Collaborator | ID | Admin | Maintain | Push | Triage | Pull |")
        lines.append("|---|---|---|---|---|---|---|")

        collab_logins = [c["login"] for c in repo.collaborators]
        sorted_logins = _sort_users(collab_logins, sort_by, user_id_map)
        login_to_collab = {c["login"]: c for c in repo.collaborators}
        collabs_sorted = [login_to_collab[login] for login in sorted_logins]
        for c in collabs_sorted:
            perms = c.get("permissions", {})
            uid = str(c.get("id", ""))
            row = [c["login"], uid]
            for level in PERMISSION_LEVELS:
                row.append("Yes" if perms.get(level) else "\u2014")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    return "\n".join(lines)
